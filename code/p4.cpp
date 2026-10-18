//求ASCII码
#include<iostream>
using namespace std;
int main()
{
	cout<<"20261113088陈依琳"<<endl;
	char ch;           //存放大写英文字母
	double ASCII;      //存放ASCII
    double n;          //存放英文字母
	cout<<"请输入一个大写英文字母：";
	cin>>ch;
	ASCII=ch+0;        //计算ASCII
	n=ch-65+1;         //计算英文字母
	cout<<"英文字母"<<ch<<"的ASCII码是"<<ASCII<<"，是26个英文字母中的第"<<n<<"个字母"<<endl;
	return 0;
}